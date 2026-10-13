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

from app.common.guid import compute_guid
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


# ── A card stored under the rejected fragment (bd tunatale-706z) ─────────────

TIRED_SENTENCE = "Trøtt"


def _setup_tired(monkeypatch, language_no, *, stored_lemma: str | None):
    """A one-word Norwegian lesson, `trøtt`, which the lemmatizer truncates to `trø`.

    *stored_lemma* is the ``lemma`` column of the deck's existing `trøtt` card,
    or ``None`` for no card. Returns ``(db, card_id)``.
    """
    import app.api.srs as srs_mod
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    stub = StubLemmatizer()
    stub.set_sentence(
        TIRED_SENTENCE,
        [TokenAnalysis(surface="Trøtt", lemma="trø", upos="ADJ", case="", number="", person="", gender="")],
    )
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

    lesson = Lesson(
        title="Day 8",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=TIRED_SENTENCE, voice_id="female-1", language_code="no", role="female-1")],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-no", "curriculum-no", 8, lesson)
    card_id = None
    if stored_lemma is not None:
        db.add_collocation(
            SyntacticUnit(
                text="trøtt", lemma=stored_lemma, translation="tired", word_count=1, difficulty=1, source="test"
            ),
            language_code="no",
        )
        card_id = db.get_collocation_id_by_guid(compute_guid("trøtt", "no", ""))
        assert card_id is not None
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = language_no
    return db, card_id


class TestPreviewFindsACardStoredUnderTheFragment:
    """The preview resolves by card key; the commit resolves by the raw lemma.

    A card whose ``lemma`` column holds the fragment (`text='trøtt'`,
    `lemma='trø'` — rows written before the plausibility screen existed, or
    imported from Anki) is found by the commit and by the reader, and was missed
    by the preview: its card key is the surface `trøtt`, the row's lemma is
    `trø`, and the surface fallback skips a surface equal to the key. So the
    preview offered a tracked word as a card to CREATE, on every listen.
    """

    async def test_the_fragment_keyed_card_is_found_not_offered_as_new(self, monkeypatch, language_no):
        db, card_id = _setup_tired(monkeypatch, language_no, stored_lemma="trø")
        assert db.get_collocation_by_id(card_id)[1].syntactic_unit.lemma == "trø"  # the seed is what it claims

        body = await _preview()

        creates = [r for r in body["candidates"] if r["kind"] == "create"]
        assert creates == [], f"a tracked word was offered as a new card: {creates}"
        rows = [r for r in body["candidates"] if r["item_id"] == card_id]
        assert [r["text"] for r in rows] == ["trøtt"], body["candidates"]

    async def test_the_commit_creates_nothing_for_the_same_lesson(self, monkeypatch, language_no):
        """The other half of the parity claim: what the preview finds, the commit finds."""
        db, card_id = _setup_tired(monkeypatch, language_no, stored_lemma="trø")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"content_id": "lesson-no"})

        assert resp.status_code == 200
        assert db.count_collocations() == 1, "the listen minted a second card for a tracked word"
        assert db.get_collocation_by_id(card_id) is not None

    async def test_a_surface_keyed_card_is_still_found(self, monkeypatch, language_no):
        """Regression guard: the row as the commit path writes it today."""
        _db, card_id = _setup_tired(monkeypatch, language_no, stored_lemma="trøtt")

        body = await _preview()

        assert [r for r in body["candidates"] if r["kind"] == "create"] == []
        assert [r["text"] for r in body["candidates"] if r["item_id"] == card_id] == ["trøtt"]

    async def test_no_card_is_still_a_create_row_named_for_the_surface(self, monkeypatch, language_no):
        """Regression guard: the fallback must not invent a card."""
        _setup_tired(monkeypatch, language_no, stored_lemma=None)

        body = await _preview()

        creates = [r for r in body["candidates"] if r["kind"] == "create"]
        assert [r["text"] for r in creates] == ["trøtt"]
