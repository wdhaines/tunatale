"""Create-row glosses in the listen preview.

A ``kind="create"`` row is an untracked lemma — there is no card yet, so there
is no ``syntactic_unit.translation`` to read and the preview used to hardcode
``translation: ""``. Every new word therefore rendered gloss-less in the modal
while every tracked word had one.

The lesson already carries the answer in
``generation_metadata["token_glosses"]``, and ``mark_lesson_listened`` already
resolves it through ``_resolve_gloss_translation`` when it creates the card.
The preview must resolve it through *that same helper* — not a private
re-implementation — so the gloss shown before the listen is by construction the
gloss stored after it. A second lookup with subtly different key ordering is
the 6a5c718 preview/commit divergence class.
"""

from __future__ import annotations

import logging

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


def _setup_lesson(phrase_text: str, token_glosses: dict[str, str] | None = None):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(
                        text=phrase_text,
                        voice_id="female-1",
                        language_code="sl",
                        role="female-1",
                    ),
                ],
            )
        ],
        key_phrases=[],
        generation_metadata={"token_glosses": token_glosses or {}},
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


async def _preview() -> list[dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()["candidates"]


def _creates(candidates: list[dict]) -> dict[str, str]:
    return {c["text"]: c["translation"] for c in candidates if c["kind"] == "create"}


class TestCreateRowGloss:
    async def test_create_row_carries_lemma_keyed_gloss(self):
        """The gloss map keyed by lemma populates the create row."""
        _setup_lesson("Banka riba", token_glosses={"banka": "bank", "riba": "fish"})

        assert _creates(await _preview()) == {"banka": "bank", "riba": "fish"}

    async def test_create_row_falls_back_to_surface_key(self):
        """A gloss keyed by the surface as it appeared still resolves.

        The LLM glosses whatever token it saw; the card is keyed by the
        lemmatizer's lemma. _resolve_gloss_translation tries lemma first, then
        surfaces — the preview inherits that for free by reusing it.
        """
        _setup_lesson("Banka riba", token_glosses={"banka": "bank"})

        creates = _creates(await _preview())
        assert creates["banka"] == "bank"
        assert creates["riba"] == "", "an unglossed lemma stays empty, not absent"

    async def test_missing_gloss_map_leaves_translations_empty(self):
        """A lesson generated before token_glosses existed must not 500."""
        _setup_lesson("Banka riba")

        assert _creates(await _preview()) == {"banka": "", "riba": ""}

    async def test_preview_gloss_equals_the_gloss_the_listen_stores(self):
        """The anti-drift property: preview shows what the commit will store.

        This is the test that would catch a private re-implementation of the
        lookup in the preview — it compares the two code paths against each
        other rather than against a hardcoded expectation.
        """
        db = _setup_lesson("Banka riba", token_glosses={"banka": "bank", "riba": "fish"})
        previewed = _creates(await _preview())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(LISTEN_URL, json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200

        for lemma, shown in previewed.items():
            item = db.get_collocation_by_lemma(lemma)
            assert item is not None, f"{lemma} should have been created by the listen"
            assert item.syntactic_unit.translation == shown, (
                f"preview showed {shown!r} for {lemma!r} but the listen stored {item.syntactic_unit.translation!r}"
            )


class TestGlossWarningBelongsToCreation:
    """The missing-gloss warning is a card-creation event, not a preview event.

    ``_resolve_gloss_translation`` warns "card created with empty translation"
    so a silently blank card is visible. The preview creates nothing and is
    re-fetched every time the modal opens, so emitting it there would both
    repeat the warning indefinitely and state something untrue.
    """

    async def test_preview_does_not_warn_about_missing_glosses(self, caplog):
        _setup_lesson("Banka riba")

        with caplog.at_level(logging.WARNING, logger="app.api.srs"):
            await _preview()

        assert [r for r in caplog.records if "empty translation" in r.message] == []

    async def test_the_listen_still_warns_when_it_creates_a_blank_card(self, caplog):
        _setup_lesson("Banka riba")

        with caplog.at_level(logging.WARNING, logger="app.api.srs"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(LISTEN_URL, json={"lesson_id": "lesson-1"})
            assert resp.status_code == 200

        warned = {r.args[0] for r in caplog.records if "empty translation" in r.message}
        assert warned == {"banka", "riba"}
