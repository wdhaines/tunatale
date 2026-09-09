"""The listen preview must key a word on the SAME lemma a listen would card it on.

bd tunatale-q5pl. The user's report was "why do I still see snøm in day 6", and
the first diagnosis — that the next listen would re-create the card — was WRONG.
`is_lemma_plausible` already rejects `snømenn` → `snøm` (widened by 6f3cf64 for
tunatale-wum6), and `mark_lesson_listened` acts on it: an implausible lemma is
re-keyed onto the surface as it appeared, so a listen resolves the EXISTING
`snømenn` card and creates nothing.

The preview never learned. It builds its candidates straight off the lemmatizer's
output, so it offered `snøm` as a brand-new word — a headword the commit path
would never use, for a card it would never create.

That is the divergence class `api/srs.py` already names in its own comments (the
6a5c718 bug), and the same file already treats parity as something to get "by
construction rather than by coincidence" for the gloss. These tests hold the
lemma to that standard: both paths go through ``_card_key_for_lemma``.

⚠️ The lemmatizer is stubbed rather than run. Reaching this needs a language with
a registered plausibility predicate (Norwegian; Slovene has none, so the screen is
a no-op there and the bug is invisible), and a real stanza load in a unit test
costs a model init for a decision that is pure string logic. Same pattern, and the
same helper, as `test_api_base_cards.py::test_truncated_lemma_falls_back_to_surface`,
which guards the commit half of this.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.syntactic_unit import SyntacticUnit
from app.srs.lemmatizer import TokenAnalysis
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests._helpers.lemmatizer import StubLemmatizer

pytestmark = pytest.mark.anyio

PREVIEW_URL = "/api/srs/content/lesson-no/listen-preview"

# One line, one word: the surface whose Norwegian lemma stanza truncates.
SENTENCE = "Snømenn"


def _setup(monkeypatch, language_no, *, seed_existing: bool):
    """A Norwegian one-word lesson whose only token lemmatizes to a fragment."""
    import app.api.srs as srs_mod
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    stub = StubLemmatizer()
    stub.set_sentence(
        SENTENCE,
        [
            TokenAnalysis(
                surface="Snømenn",
                lemma="snøm",
                upos="NOUN",
                case="",
                number="Plur",
                person="",
                gender="Masc",
            )
        ],
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
    if seed_existing:
        # The card a previous listen actually made — keyed on the surface,
        # because the commit path already re-keys an implausible lemma.
        db.add_collocation(
            SyntacticUnit(text="snømenn", translation="snowmen", word_count=1, difficulty=1, source="test"),
            language_code="no",
        )
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = language_no
    return db


async def _preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


class TestPreviewKeysOnTheSameLemmaAsCommit:
    async def test_the_truncated_lemma_is_never_offered(self, monkeypatch, language_no):
        """`snøm` is not a word and no card would ever be keyed on it."""
        _setup(monkeypatch, language_no, seed_existing=False)

        body = await _preview()

        assert not any(row["text"] == "snøm" for row in body["candidates"]), (
            f"preview offered the truncated lemma: {body['candidates']}"
        )

    async def test_a_create_row_shows_the_surface_the_card_would_use(self, monkeypatch, language_no):
        """With no existing card, the offered headword is the surface as it appeared."""
        _setup(monkeypatch, language_no, seed_existing=False)

        body = await _preview()

        creates = [r for r in body["candidates"] if r["kind"] == "create"]
        assert [r["text"] for r in creates] == ["snømenn"]

    async def test_an_existing_surface_card_is_found_not_re_offered(self, monkeypatch, language_no):
        """THE reported bug, and it is a LABEL bug, not a creation bug.

        ⚠️ The lookup already SUCCEEDS: `_resolve_card_for_lemma` finds the
        `snømenn` card from the fragment via the inflection index, which is why
        the live row carried that card's own translation ("snowmen") while
        showing `snøm` as its text. Nothing was ever mis-created — `snøm` has
        been absent from `collocations` since it was graved. The row simply named
        a non-word, on every listen, indefinitely.
        """
        _setup(monkeypatch, language_no, seed_existing=True)

        body = await _preview()

        creates = [r for r in body["candidates"] if r["kind"] == "create"]
        assert creates == [], f"an existing card was offered as new: {creates}"
        assert any(r["text"] == "snømenn" for r in body["candidates"])
